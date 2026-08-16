"""Ders Takvimi — giriş noktası.

Hiçbir yetenek SAĞLAMAZ; yalnız `bell.week` tüketir. 0.1'de tersiydi.
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import ScheduleService


def register(ctx: ModuleContext) -> None:
    # optional: Zil Sistemi düşse bile bu ekran ayakta kalır ve nedenini
    # gösterir. Boş takvim ile ulaşılamayan takvim aynı şey değildir (K7).
    read_week = ctx.try_capability("bell.week")
    if read_week is None:
        ctx.log.warning("zil saatleri yeteneği yok — ekran nedeniyle açılacak")

    service = ScheduleService(log=ctx.log, read_week=read_week)
    ctx.add_router(bind(service))
    ctx.log.info("ders takvimi hazır (salt okunur)")
