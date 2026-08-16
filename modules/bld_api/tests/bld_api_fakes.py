"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; geçidin yazdığı iki ifadeyi (denetim satırı ve
denetim güncellemesi) tanıyacak kadarını yapar. Amaç, çekirdek depoyu taklit
etmek değil, geçidin doğru anda doğru satırı yazdığını görmek.
"""

from __future__ import annotations

from typing import Any

import httpx
from bld_api_backend.client import SECRET_KEY, BldApi

#: Testlerde kullanılan imza sırrı. Gerçek sır kasadadır ve depoda bulunmaz.
TEST_SECRET = "kontrol-merkezi-test-sirri"


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

    def __init__(self, module_id: str = "bld_api") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.fail = False

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if self.fail:
            raise RuntimeError("depo kapalı")
        text = " ".join(sql.split())
        if "mod_bld_api_audit" in text and text.startswith("INSERT"):
            columns = ("request_id", "method", "path", "action", "reason", "actor",
                       "dry_run", "body", "created_at")
            self.audit.append(dict(zip(columns, params, strict=True)) | {"result": "",
                                                                        "status": None})
        elif "mod_bld_api_audit" in text and text.startswith("UPDATE"):
            result, status, finished_at, request_id = params
            for row in self.audit:
                if row["request_id"] == request_id:
                    row.update(result=result, status=status, finished_at=finished_at)
        else:  # pragma: no cover - testlerde başka ifade yok
            raise AssertionError(f"beklenmeyen SQL: {text}")

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if self.fail:
            raise RuntimeError("depo kapalı")
        if "mod_bld_api_audit" in sql:
            return list(reversed(self.audit))[: params[0]]
        return []  # pragma: no cover


# Testler gerçekten beklemesin: geçidin uykusu yutulur.
async def _uyuma(_seconds: float) -> None:
    return None


def gateway(handler: Any, **options: Any) -> tuple[BldApi, FakeStore, FakeLog, FakeSecrets]:
    """Sahte taşıyıcıyla geçit. Varsayılanlar testler için AÇIK yazma kipinde.

    `httpx.MockTransport` sayesinde hiçbir test ağa çıkmaz; istemci
    `transport=` kurucu parametresini bu yüzden alıyor.
    """
    depo, gunluk = FakeStore(), FakeLog()
    kasa = FakeSecrets({SECRET_KEY: TEST_SECRET})
    ayar: dict[str, Any] = {"read_only": False, "dry_run_default": False}
    ayar.update(options)
    api = BldApi(base_url="https://ornek.test", secrets=kasa, log=gunluk, store=depo,
                 transport=httpx.MockTransport(handler), **ayar)
    api._sleep = _uyuma
    return api, depo, gunluk, kasa
