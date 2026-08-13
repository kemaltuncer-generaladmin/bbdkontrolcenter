"""Olay veri yolu — modüller arası gevşek bağ (K3).

Yayınlayan, dinleyeni bilmez. Bir dinleyicinin patlaması ne yayınlayanı ne de
diğer dinleyicileri düşürür (K7): hata yakalanır, loglanır, akış sürer.
"""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

Handler = Callable[[str, dict[str, Any]], Awaitable[None] | None]

log = structlog.get_logger("km.bus")


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[tuple[str, Handler]]] = defaultdict(list)

    def subscribe(self, event: str, handler: Handler, *, subscriber: str) -> None:
        self._handlers[event].append((subscriber, handler))

    def unsubscribe_all(self, subscriber: str) -> None:
        for event, handlers in list(self._handlers.items()):
            self._handlers[event] = [item for item in handlers if item[0] != subscriber]

    async def publish(self, event: str, payload: dict[str, Any] | None = None) -> None:
        data = payload or {}
        for subscriber, handler in list(self._handlers.get(event, [])):
            try:
                result = handler(event, data)
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 — sınır burada bilinçli geniş
                log.error("olay dinleyici hatası", event=event, subscriber=subscriber, error=str(error))

    def subscribers(self, event: str) -> list[str]:
        return [subscriber for subscriber, _ in self._handlers.get(event, [])]
