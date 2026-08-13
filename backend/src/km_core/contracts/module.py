"""Modül sözleşmesi — `register(ctx)` fonksiyonuna verilen bağlam.

Modül çekirdeği import etmez (K2); bu tipleri `km_sdk` üzerinden görür.
Bağlam, modülün çekirdekle konuşabildiği TEK yüzeydir: registry, olay yolu,
kendi ayarı, kendi tabloları ve kendi router'ı.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover - yalnızca tip denetimi
    from fastapi import APIRouter

    from km_core.bus.bus import EventBus
    from km_core.registry.registry import Registry
    from km_core.store.db import Store


class ModuleStore(Protocol):
    """Modülün kendi tablolarına erişimi. Tablo adı önekle korunur (K5)."""

    def table(self, name: str) -> str: ...
    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]: ...
    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None: ...
    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None: ...


@dataclass(slots=True)
class ScopedStore:
    """`Store`un modüle açılan yüzü. Tablo adlarını modül önekiyle üretir."""

    _store: Store
    module_id: str

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return await self._store.fetch_all(sql, params)

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        return await self._store.fetch_one(sql, params)

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        await self._store.execute(sql, params)


@dataclass(slots=True)
class ModuleContext:
    """`register(ctx)` içine gelen bağlam."""

    module_id: str
    module_path: Path
    config: dict[str, Any]
    store: ScopedStore
    log: Any
    _registry: Registry
    _bus: EventBus
    _declared_provides: set[str]
    _declared_consumes: set[str]
    routers: list[APIRouter] = field(default_factory=list)

    # ------------------------------------------------------------ yetenek

    def provide(self, capability: str, instance: Any) -> None:
        """Yeteneği deftere yazar. Manifestte ilan edilmemişse reddedilir."""
        if capability not in self._declared_provides:
            raise ValueError(
                f"{self.module_id}: '{capability}' manifestte `provides` ile ilan edilmemiş."
            )
        self._registry.register(capability, instance, provider=self.module_id)

    def capability(self, name: str) -> Any:
        """Yeteneği çözer. Manifestte `consumes` ile ilan edilmemişse reddedilir."""
        if name not in self._declared_consumes:
            raise ValueError(
                f"{self.module_id}: '{name}' manifestte `consumes` ile ilan edilmemiş."
            )
        return self._registry.resolve(name)

    def try_capability(self, name: str) -> Any | None:
        if name not in self._declared_consumes:
            raise ValueError(
                f"{self.module_id}: '{name}' manifestte `consumes` ile ilan edilmemiş."
            )
        return self._registry.try_resolve(name)

    # --------------------------------------------------------------- olay

    def subscribe(self, event: str, handler: Callable[[str, dict[str, Any]], Awaitable[None] | None]) -> None:
        self._bus.subscribe(event, handler, subscriber=self.module_id)

    async def publish(self, event: str, payload: dict[str, Any] | None = None) -> None:
        await self._bus.publish(event, payload)

    # --------------------------------------------------------------- http

    def add_router(self, router: APIRouter) -> None:
        self.routers.append(router)
