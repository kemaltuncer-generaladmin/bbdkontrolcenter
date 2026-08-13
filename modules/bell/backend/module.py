"""Zil Sistemi — giriş noktası."""

from __future__ import annotations

import asyncio

from km_sdk import ModuleContext

from .api.routes import bind
from .service import BellService


def register(ctx: ModuleContext) -> None:
    # Ses ve zamanlayıcı platform yetenekleridir; ikisi de olmadan zil çalamaz.
    audio = ctx.capability("audio")
    scheduler = ctx.capability("scheduler")
    # Ders saatleri Ders Takvimi modülünündür — kopyalanmaz, okunur (K3).
    # Modül kapalıysa zil ayakta kalır, ekran boş takvimle açılır (K7).
    schedule_reader = ctx.try_capability("bbd_class_schedule.week")

    service = BellService(store=ctx.store, log=ctx.log, audio=audio,
                          scheduler=scheduler, schedule_reader=schedule_reader)
    ctx.add_router(bind(service))
    ctx.provide("bell.schedule", service.state)

    # Tetikleyici tablosu açılışta bir kez kurulur. `register` bildirim aşamasıdır
    # ve I/O yapmaz; bu yüzden tablo kurulumu çalışan döngüye görev olarak bırakılır
    # ve çekirdek açılışını bekletmez. Zamanlayıcı zaten planları topladıktan sonra
    # başlıyor, ilk tur dolu tabloyla döner.
    asyncio.get_running_loop().create_task(_bootstrap(service, ctx.log))
    ctx.log.info("zil sistemi hazır")


async def _bootstrap(service: BellService, log: object) -> None:
    try:
        result = await service.reschedule()
        log.info("zil tetikleyicileri kuruldu", triggers=result.get("triggers"))
    except Exception as failure:  # noqa: BLE001 — açılış görevi çekirdeği düşürmez (K7)
        log.error("zil tetikleyicileri kurulamadı", error=str(failure))
