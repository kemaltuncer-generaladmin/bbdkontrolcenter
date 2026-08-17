"""Depo sözleşmesi — iki motorun ortak yüzeyi.

`Store` (SQLite) ve `PostgresStore` bunu YAPISAL olarak karşılar; ikisi de
buradan türemez, kalıtım yoktur. Çekirdeğin geri kalanı somut sınıfa değil bu
protokole bakar, böylece "hangi motor" sorusu tek bir yerde
(`km_core/store/engine.py`) kalır.

Sözleşmenin ayrı bir dosyada durmasının sebebi döngüsel import: `db.py` ve
`postgres.py` bu protokolü kullananları import etmez, kullananlar da somut
sınıfları import etmek zorunda kalmaz.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StoreLike(Protocol):
    """Çekirdeğin depodan beklediği her şey."""

    # --------------------------------------------------------- yaşam döngüsü
    async def open(self) -> None: ...
    async def close(self) -> None: ...

    # ---------------------------------------------------------------- sorgu
    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]: ...
    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None: ...
    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None: ...
    async def execute_many(self, sql: str, rows: Iterable[Sequence[Any]]) -> None: ...

    # ------------------------------------------------------------------ şema
    async def execute_script(self, sql: str) -> None: ...
    async def table_columns(self, table: str) -> set[str]: ...
    async def applied_migrations(self, owner: str) -> set[str]: ...
    async def apply_migration(self, owner: str, name: str, sql: str) -> None: ...
