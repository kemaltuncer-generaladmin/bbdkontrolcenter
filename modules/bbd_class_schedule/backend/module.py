"""Ders Takvimi — giriş noktası."""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import ScheduleService


def register(ctx: ModuleContext) -> None:
    service = ScheduleService(store=ctx.store, log=ctx.log)
    ctx.add_router(bind(service))
    # Zil sistemi ders saatlerini buradan okur; kopyalamaz (K3).
    ctx.provide("bbd_class_schedule.week", service.week)
    ctx.log.info("ders takvimi hazır")
