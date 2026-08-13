"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; geçidin yazdığı üç ifadeyi (snapshot upsert,
denetim satırı, denetim güncellemesi) tanıyacak kadarını yapar. Amaç, çekirdek
depoyu taklit etmek değil, geçidin doğru anda doğru satırı yazdığını görmek.
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

    def text(self) -> str:
        return " ".join(f"{message} {fields}" for _, message, fields in self.records)


class FakeSecrets:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})
        self.reads: list[str] = []

    async def get(self, key: str) -> str | None:
        self.reads.append(key)
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value


class FakeStore:
    """`ModuleStore` yüzeyi. Satırları bellekte tutar."""

    def __init__(self, module_id: str = "store_api") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.fail = False

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if self.fail:
            raise RuntimeError("depo kapalı")
        text = " ".join(sql.split())
        if "mod_store_api_snapshot" in text and text.startswith("INSERT"):
            key, payload, stored_at = params
            self.snapshots[key] = {"payload": payload, "stored_at": stored_at}
        elif "mod_store_api_audit" in text and text.startswith("INSERT"):
            columns = ("request_id", "method", "path", "action", "reason", "actor",
                       "dry_run", "body", "created_at")
            self.audit.append(dict(zip(columns, params, strict=True)) | {"result": "",
                                                                        "status": None})
        elif "mod_store_api_audit" in text and text.startswith("UPDATE"):
            result, status, finished_at, request_id = params
            for row in self.audit:
                if row["request_id"] == request_id:
                    row.update(result=result, status=status, finished_at=finished_at)
        else:  # pragma: no cover - testlerde başka ifade yok
            raise AssertionError(f"beklenmeyen SQL: {text}")

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        if self.fail:
            raise RuntimeError("depo kapalı")
        if "mod_store_api_snapshot" in sql:
            return self.snapshots.get(params[0])
        return None  # pragma: no cover

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if self.fail:
            raise RuntimeError("depo kapalı")
        if "mod_store_api_audit" in sql:
            return list(reversed(self.audit))[: params[0]]
        return []  # pragma: no cover
