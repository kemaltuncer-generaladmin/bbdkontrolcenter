"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeApi` yalnız bu modülün kullandığı dört `store.api` metodunu taşır:
`bbd_audit`, `bbd_audit_entry`, `audit_trail`, `admin_users`. Geçidin geri
kalanı bu ekranı ilgilendirmez — salt okunur ekranın yazma metodu yoktur.
"""

from __future__ import annotations

from typing import Any


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


class FakeStore:
    """`ModuleStore` yüzeyi. Yalnız döküm izi tablosunu tanır."""

    def __init__(self, module_id: str = "store_udit_logs") -> None:
        self.module_id = module_id
        self.exports: list[dict[str, Any]] = []
        self.fail = False

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if self.fail:
            raise RuntimeError("disk dolu")
        if "_exports" in sql and sql.strip().upper().startswith("INSERT"):
            keys = ("kind", "range_start", "range_end", "rows", "path", "actor", "created_at")
            self.exports.append(dict(zip(keys, params, strict=False)))

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if self.fail:
            raise RuntimeError("okunamadı")
        if "_exports" in sql:
            return list(reversed(self.exports))
        return []


class FakeApi:
    """`store.api` yeteneğinin testlik yüzü."""

    def __init__(self, *, remote: list[dict[str, Any]] | None = None,
                 local: list[dict[str, Any]] | None = None) -> None:
        #: Uzak denetim tablosu — zamana göre AZALAN sırada verilmeli.
        self.remote = remote or []
        #: Geçidin yerel izi (`mod_store_api_audit` satırları).
        self.local = local or []
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        #: True ise sunucu süzgeci YOK SAYAR (Laravel'in gerçek davranışı).
        self.ignore_filters = False
        self.page_size = 50

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    async def bbd_audit(self, filters: Any = None, *, page: int = 1,
                        per_page: int | None = None) -> dict[str, Any]:
        self._record("bbd_audit", filters, page=page, per_page=per_page)
        rows = self.remote
        if not self.ignore_filters:
            rows = [row for row in rows if _keep(row, filters or {})]
        size = min(per_page or self.page_size, self.page_size)
        start = (max(1, page) - 1) * size
        window = rows[start:start + size]
        last_page = max(1, (len(rows) + size - 1) // size)
        return {"items": window,
                "meta": {"total": len(rows), "perPage": size, "currentPage": page,
                         "lastPage": last_page},
                "truncated": False}

    async def bbd_audit_entry(self, entry_id: int) -> dict[str, Any]:
        self._record("bbd_audit_entry", entry_id)
        for row in self.remote:
            if int(row.get("id") or 0) == int(entry_id):
                return row
        raise RuntimeError("Kayıt bulunamadı")

    async def audit_trail(self, *, limit: int = 100) -> list[dict[str, Any]]:
        self._record("audit_trail", limit=limit)
        return self.local[:limit]

    async def admin_users(self) -> dict[str, Any]:
        self._record("admin_users")
        return {"items": [{"id": 1, "name": "Ayşe Yılmaz"}, {"id": 2, "name": "Mehmet Kaya"}]}


def _keep(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    """Sunucunun uyguladığı süzgeç — yalnız tarih ve varlık, gerisi yok sayılır
    değil, tam uygulanır; `ignore_filters` ile kapatılabilir."""
    # Mağaza damgası boşluklu ("2026-08-13 10:22:31"), süzgeç ISO ("…T10:22:31"):
    # metin karşılaştırması yapmadan önce aynı eksene getirilir.
    at = str(row.get("created_at") or "").replace(" ", "T")
    if filters.get("from") and at < str(filters["from"]):
        return False
    if filters.get("to") and at > str(filters["to"]):
        return False
    wanted = filters.get("entity_id")
    return not wanted or int(row.get("auditable_id") or 0) == int(wanted)
