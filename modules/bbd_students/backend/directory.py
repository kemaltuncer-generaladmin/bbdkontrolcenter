"""`bbd_students.directory` yeteneği — öğrenci dizini (mobil geçit sözleşmesi §1).

Kontrol Merkezi öğrencinin ana kaydıdır. Bu yetenek kantindeki kayıtla
(ad, veli 1 telefonu, bakiye, limit, engel) modülün kendi profilini (ad/soyad,
sınıf, okul no, öğrenci telefonu, veli 2, seviye/alan/hedef sınav) birleştirip
sözleşmenin öğrenci nesnesini (`StudentRecord`) üretir. Başka modüller onu
registry üzerinden çözer (K3); tabloyu okumazlar (K5).

    list(since=None)      → {"students": [StudentRecord], "classes": [str], "complete": bool}
    get(student_id)       → StudentRecord | None
    find_by_phone(phone10)→ [StudentRecord]
    card_qr_text(id)      → str   (kartta basılı v1 QR'ın aynısı; anahtar dönmez)

Hepsi `async`tır (kantin çağrısı yapar). Kantine ulaşılamazsa:
`list` hata YÜKSELTMEZ, `complete: false` ve boş liste döner — istemci eksik
öğrenciyi silinmiş saymaz. `get` / `find_by_phone` / `card_qr_text` ise
`DirectoryUnavailable` yükseltir (`reason == "canteen_unavailable"`); çağıran
modül sınıfı import edemez (K3), `getattr(hata, "reason", None)` ile ayırt eder.

StudentRecord YALNIZ şu anahtarları taşır — başka hiçbir şey sızmaz
(`accessCode` asla):

    id, firstName, lastName, displayName, className, schoolNo, studentPhone,
    guardians[{slot, name, phone}], canteen{balance, spendingLimit, isBlocked,
    dailyLimit, guardianBlocked}, updatedAt, phoneIssues[],
    gradeLevel, track, examTarget
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from . import curriculum
from . import qr as qr_codec
from .phones import normalize_phone

_ISTANBUL = ZoneInfo("Europe/Istanbul")

#: Kantin listesinin bellekte tutulduğu süre (saniye). Telefonla arama ve
#: dizin aynı dakikada birkaç kez çağrılabilir; her biri kantinin tam
#: listesini çekmesin. Kısa tutulur: veri kantinde canlıdır.
CACHE_SECONDS = 20.0

#: Sözleşmenin öğrenci nesnesindeki anahtarlar — sıra da sözleşmeninkidir.
RECORD_KEYS = (
    "id", "firstName", "lastName", "displayName", "className", "schoolNo",
    "studentPhone", "guardians", "canteen", "updatedAt", "phoneIssues",
    "gradeLevel", "track", "examTarget",
)


#: Kayıt listesi. Sınıfın `list` metodu tür adını gölgelemesin diye modül
#: düzeyinde adlandırılır.
Records = list[dict[str, Any]]


class DirectoryUnavailable(RuntimeError):
    """Kantine ulaşılamadı. `reason` makine içindir."""

    reason = "canteen_unavailable"


def split_name(display_name: str) -> tuple[str, str]:
    """Kantin tek alan tutuyor; profil yoksa son kelime soyad sayılır."""
    parts = display_name.strip().split()
    if len(parts) < 2:
        return display_name.strip(), ""
    return " ".join(parts[:-1]), parts[-1]


def _moment(value: Any) -> datetime | None:
    """Epoch-ms (kantin) ya da ISO (profil) → tz'li datetime."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000.0, tz=UTC)
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(_ISTANBUL).isoformat(timespec="seconds") if moment else None


def student_record(canteen: dict[str, Any], profile: dict[str, Any] | None) -> dict[str, Any]:
    """Kantin satırı + profil satırı → sözleşmenin öğrenci nesnesi."""
    profile = profile or {}
    display_name = str(canteen.get("displayName") or "").strip()
    first, last = split_name(display_name)
    issues: list[str] = []

    def phone(field: str, raw: Any) -> str | None:
        normalized = normalize_phone(raw)
        if normalized is None and str(raw or "").strip():
            issues.append(field)
        return normalized

    student_phone = phone("studentPhone", profile.get("student_phone"))

    guardians: list[dict[str, Any]] = []
    # Veli 1 = kantinin `parentPhone`u + profilin `parent_name`i.
    raw1 = canteen.get("parentPhone")
    if str(raw1 or "").strip():
        guardians.append({"slot": 1, "name": str(profile.get("parent_name") or "") or None,
                          "phone": phone("parentPhone", raw1)})
    # Veli 2 = profilin `parent_name2` + `parent_phone2`si.
    raw2 = profile.get("parent_phone2")
    if str(raw2 or "").strip():
        guardians.append({"slot": 2, "name": str(profile.get("parent_name2") or "") or None,
                          "phone": phone("parentPhone2", raw2)})

    stamps = [m for m in (_moment(canteen.get("updatedAt")), _moment(profile.get("updated_at")))
              if m is not None]
    plan = curriculum.from_row(profile)

    record = {
        "id": str(canteen.get("id") or ""),
        "firstName": str(profile.get("first_name") or "") or first,
        "lastName": str(profile.get("last_name") or "") or last,
        "displayName": display_name,
        "className": str(profile.get("class_name") or ""),
        "schoolNo": str(profile.get("school_no") or ""),
        "studentPhone": student_phone,
        "guardians": guardians,
        "canteen": {
            "balance": int(canteen.get("balance") or 0),
            "spendingLimit": canteen.get("spendingLimit"),
            "isBlocked": bool(canteen.get("isBlocked")),
            "dailyLimit": canteen.get("dailyLimit"),
            "guardianBlocked": bool(canteen.get("guardianBlocked")),
        },
        "updatedAt": _iso(max(stamps) if stamps else None),
        "phoneIssues": issues,
        "gradeLevel": plan["gradeLevel"],
        "track": plan["track"],
        "examTarget": plan["examTarget"],
    }
    return {key: record[key] for key in RECORD_KEYS}


def _class_key(name: str) -> tuple[int, str]:
    digits = ""
    for char in name:
        if not char.isdigit():
            break
        digits += char
    return (int(digits) if digits else 999, name.casefold())


class StudentDirectory:
    """Yeteneğin uygulaması. Kantin ve modülün kendi deposu dışında bağı yoktur."""

    def __init__(self, *, canteen: Any, store: Any, log: Any) -> None:
        self._canteen = canteen
        self._store = store
        self._log = log
        self._table = store.table("profile")
        self._cache: tuple[float, Records] | None = None

    async def _canteen_students(self) -> Records:
        now = time.monotonic()
        if self._cache is not None and now - self._cache[0] < CACHE_SECONDS:
            return self._cache[1]
        try:
            rows = list(await self._canteen.students())
        except Exception as failure:  # kantin dışarısı; sebebi taşınır
            self._log.warning("dizin: kantin okunamadı", error=str(failure))
            raise DirectoryUnavailable("Kantine ulaşılamadı.") from failure
        self._cache = (now, rows)
        return rows

    async def _profiles(self) -> dict[str, dict[str, Any]]:
        rows = await self._store.fetch_all(f"SELECT * FROM {self._table}")
        return {str(row["kantin_id"]): row for row in rows}

    async def _records(self) -> Records:
        students = await self._canteen_students()
        profiles = await self._profiles()
        return [
            student_record(row, profiles.get(str(row.get("id") or "")))
            for row in students
            if row.get("id")
        ]

    async def list(self, since: datetime | None = None) -> dict[str, Any]:
        try:
            records = await self._records()
        except DirectoryUnavailable:
            return {"students": [], "classes": [], "complete": False}

        classes = sorted({row["className"] for row in records if row["className"]},
                         key=_class_key)
        if since is not None:
            threshold = since if since.tzinfo else since.replace(tzinfo=UTC)
            records = [
                row for row in records
                if (stamp := _moment(row["updatedAt"])) is not None and stamp > threshold
            ]
        return {"students": records, "classes": classes, "complete": True}

    async def get(self, student_id: str) -> dict[str, Any] | None:
        wanted = str(student_id or "").strip()
        if not wanted:
            return None
        try:
            canteen = await self._canteen.student(wanted)
        except Exception as failure:  # kantin dışarısı; sebebi taşınır
            if getattr(failure, "status", None) == 404 \
                    or getattr(failure, "reason", None) == "student_not_found":
                return None
            self._log.warning("dizin: öğrenci okunamadı", student=wanted, error=str(failure))
            raise DirectoryUnavailable("Kantine ulaşılamadı.") from failure
        if not isinstance(canteen, dict) or str(canteen.get("id") or "") != wanted:
            return None
        profile = await self._store.fetch_one(
            f"SELECT * FROM {self._table} WHERE kantin_id = ?", (wanted,)
        )
        return student_record(canteen, profile)

    async def find_by_phone(self, phone10: str) -> Records:
        """Öğrenci telefonu, veli 1 ya da veli 2 telefonu eşleşen öğrenciler."""
        wanted = normalize_phone(phone10)
        if wanted is None:
            return []
        found = []
        for record in await self._records():
            phones = {record["studentPhone"]} | {g["phone"] for g in record["guardians"]}
            if wanted in phones:
                found.append(record)
        return found

    async def card_qr_text(self, student_id: str) -> str:
        """Kartta basılı v1 QR metni. QR anahtarı sunucuda kalır, dışarı çıkmaz."""
        try:
            key = await self._canteen.qr_key()
        except Exception as failure:  # kantin dışarısı; sebebi taşınır
            raise DirectoryUnavailable("QR anahtarı alınamadı.") from failure
        return qr_codec.encode(str(student_id), key)
