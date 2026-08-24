"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB/KANTİN KULLANMAZ.

`FakeCanteen` yalnız `StudentService`'in ÇAĞIRDIĞI metotları taşır.
`fail_for` kümesine bir `opaque_id` konursa o öğrencinin kod sıfırlaması
patlar — kısmi hata testinin dayandığı yer budur.
"""

from __future__ import annotations

from typing import Any


class FakeLog:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    def info(self, message: str, **fields: Any) -> None:
        self.records.append(("info", message, fields))

    def warning(self, message: str, **fields: Any) -> None:
        self.records.append(("warning", message, fields))

    def error(self, message: str, **fields: Any) -> None:
        self.records.append(("error", message, fields))


class FakeStore:
    """`ModuleStore` yüzeyi. `profile` tablosunu bellekte tutar."""

    def __init__(self, profiles: list[dict[str, Any]] | None = None) -> None:
        self.profiles = list(profiles or [])
        self.written: list[tuple[str, tuple[Any, ...]]] = []

    def table(self, name: str) -> str:
        return f"mod_bbd_students_{name}"

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return list(self.profiles)

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = await self.fetch_all(sql, params)
        return rows[0] if rows else None

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.written.append((" ".join(sql.split()), params))


class FakeCanteen:
    """`canteen.api` yeteneğinin testlik yüzü."""

    def __init__(self, students: list[dict[str, Any]] | None = None) -> None:
        self.student_list = list(students or [])
        self.issued: list[str] = []
        self.fail_for: set[str] = set()
        self._counter = 0

    async def students(self) -> list[dict[str, Any]]:
        return list(self.student_list)

    async def dashboard(self) -> dict[str, Any]:
        return {}

    async def status(self) -> dict[str, Any]:
        return {"ok": True}

    async def qr_key(self) -> bytes:
        return b"test-key"

    async def upsert_student(self, opaque_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        return {"id": opaque_id, **changes}

    async def issue_student_access_code(self, opaque_id: str) -> dict[str, Any]:
        self.issued.append(opaque_id)
        if opaque_id in self.fail_for:
            raise RuntimeError(f"{opaque_id} için kantin kod döndürmedi")

        self._counter += 1
        student = next((row for row in self.student_list if row.get("id") == opaque_id), None)
        name = str((student or {}).get("displayName") or "")
        return {"studentName": name, "accessCode": f"{self._counter:06d}"}


def profile_row(kantin_id: str, *, class_name: str = "") -> dict[str, Any]:
    """`profile` tablosundan gelen ham satır — yalnız testte kullanılan alanlar dolu."""
    return {
        "kantin_id": kantin_id, "first_name": "", "last_name": "",
        "class_name": class_name, "school_no": "", "student_phone": "",
        "parent_name": "", "parent_name2": "", "parent_phone2": "", "note": "",
    }


def student(opaque_id: str, name: str, *, access_code: str = "", balance: int = 0,
            blocked: bool = False) -> dict[str, Any]:
    """Kantinin döndürdüğü biçimde ham öğrenci — `StudentResource` taklidi."""
    return {
        "id": opaque_id,
        "displayName": name,
        "parentPhone": "",
        "balance": balance,
        "spendingLimit": None,
        "isBlocked": blocked,
        "updatedAt": "",
        "accessCode": access_code,
        "accessCodeIssuedAt": None,
    }
