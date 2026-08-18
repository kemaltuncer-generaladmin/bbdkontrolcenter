"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeCanteen` kantinin YALNIZ üç ucunu taklit eder ama iki davranışını
gerçekten uygular: `limit` süzgeci (satır sınırı testlerinin anlamı buna
bağlı) ve `from`/`to` yarı açık aralığı. Sınırı uygulamayan bir sahte,
"sınıra dayanınca uyar" kuralını hiç sınamazdı.

`FakeStore` SQL'i ayrıştırmaz: servis yalnız iz kaydı yazıyor, testlerin
sorusu "hangi satır yazıldı" değil "rapor ne döndü".
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def at(day: str, hour: int = 10, minute: int = 0) -> int:
    """Yerel gün + saat → epoch-ms.

    Servis gün sınırlarını naive `datetime.fromisoformat` ile hesaplıyor, yani
    MAKİNENİN yerel saatiyle. Test verisi de aynı yolla üretilmeli; UTC'den
    kurulan bir zaman damgası akşam işlemlerini komşu güne kaydırır ve testi
    saat dilimine göre kırılgan yapar.
    """
    return int(datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00").timestamp() * 1000)


def sale(day: str, *, student: str = "s1", total: int = 1000, hour: int = 10,
         minute: int = 0, name: str = "Ayşe", items: list[dict[str, Any]] | None = None,
         reversed_at: int | None = None, reason: str = "") -> dict[str, Any]:
    """Kantin biçiminde tek satış satırı."""
    row: dict[str, Any] = {
        "serverId": f"{student}-{day}-{hour:02d}{minute:02d}",
        "studentId": student,
        "studentName": name,
        "total": total,
        "createdAt": at(day, hour, minute),
        "reversedAt": reversed_at,
        "reversedReason": reason,
        "items": items if items is not None else [
            {"productId": 7, "name": "Süt", "qty": 1, "lineTotal": total},
        ],
    }
    return row


class FakeLog:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    def _add(self, level: str, message: str, **fields: Any) -> None:
        self.records.append((level, message, fields))

    def info(self, message: str, **fields: Any) -> None:
        self._add("info", message, **fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._add("warning", message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        self._add("error", message, **fields)

    def messages(self, level: str | None = None) -> list[str]:
        return [message for kind, message, _ in self.records if level in (None, kind)]


class FakeStore:
    """`ModuleStore` yüzeyi. İz kaydını bellekte tutar."""

    def __init__(self, module_id: str = "bbd_canteen_reports") -> None:
        self.module_id = module_id
        self.days: dict[str, dict[str, Any]] = {}

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        text = " ".join(sql.split())
        if "_day" in text and text.startswith("INSERT"):
            day, payload, count, total, fetched = params
            self.days[str(day)] = {"payload": payload, "count": count,
                                   "total": total, "fetchedAt": fetched}

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return []


class FakeCanteen:
    """`canteen.api` yeteneğinin testlik yüzü — yalnız raporun kullandığı uçlar."""

    def __init__(self, rows: list[dict[str, Any]] | None = None,
                 collections: Any = None,
                 students: list[dict[str, Any]] | None = None,
                 products: list[dict[str, Any]] | None = None) -> None:
        self.rows = list(rows or [])
        self.collections_payload: Any = collections if collections is not None else {}
        self.students = list(students or [])
        self.products = list(products or [])
        #: Patlatılacak uçlar — K7 davranışını (biri düşse de rapor gelir) sınamak için.
        self.fail: set[str] = set()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def count(self, endpoint: str) -> int:
        return sum(1 for name, _ in self.calls if name == endpoint)

    async def transactions(self, *, from_ms: int | None = None, to_ms: int | None = None,
                           limit: int | None = None) -> list[dict[str, Any]]:
        self.calls.append(("transactions", {"from": from_ms, "to": to_ms, "limit": limit}))
        if "transactions" in self.fail:
            raise RuntimeError("kantin yanıt vermedi")
        picked = [
            row for row in self.rows
            if (from_ms is None or int(row.get("createdAt") or 0) >= from_ms)
            and (to_ms is None or int(row.get("createdAt") or 0) < to_ms)
        ]
        picked.sort(key=lambda row: int(row.get("createdAt") or 0))
        # SINIR GERÇEKTEN UYGULANIR: kantin de fazlasını vermez.
        return picked[:limit] if limit else picked

    async def collections(self, *, from_ms: int, to_ms: int,
                          student_id: str | None = None) -> Any:
        self.calls.append(("collections", {"from": from_ms, "to": to_ms,
                                           "studentId": student_id}))
        if "collections" in self.fail:
            raise RuntimeError("tahsilat raporu okunamadı")
        payload = self.collections_payload
        if student_id and isinstance(payload, dict) and isinstance(payload.get("entries"), list):
            return {**payload, "entries": [
                item for item in payload["entries"]
                if str(item.get("studentId") or student_id) == student_id
            ]}
        return payload

    async def snapshot(self) -> dict[str, Any]:
        self.calls.append(("snapshot", {}))
        if "snapshot" in self.fail:
            raise RuntimeError("özet okunamadı")
        return {"students": self.students, "products": self.products,
                "dashboard": {}, "errors": []}
