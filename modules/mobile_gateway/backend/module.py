"""Mobil Geçit — giriş noktası.

`register(ctx)` iş yapmaz: ağa çıkmaz, DB'ye yazmaz. Yetenekler burada
ÇÖZÜLMEZ; `Capabilities` her istekte `ctx.try_capability` ile çözer. Böylece
isteğe bağlı bir sağlayıcı (tablet, zil) düşse ya da sonradan yüklense bile
geçit ayakta kalır ve yalnız ilgili uç 503 `capability_missing` döner (K7).

Olay iletimi (KM → Akademi) açılışta çalışan döngüye görev olarak bırakılır;
`events_enabled: false` (varsayılan) iken hiçbir istek atılmaz.
"""

from __future__ import annotations

import asyncio
from typing import Any

from km_sdk import ModuleContext

from .api.routes import bind
from .caps import READ_TIMEOUT, WRITE_TIMEOUT, Capabilities
from .events import EventSender, GatewayEvents
from .service import GatewayService


def register(ctx: ModuleContext) -> None:
    timeouts = ctx.config.get("timeouts") or {}
    service = GatewayService(
        store=ctx.store,
        caps=Capabilities(
            ctx.try_capability,
            read_timeout=float(timeouts.get("read_seconds") or READ_TIMEOUT),
            write_timeout=float(timeouts.get("write_seconds") or WRITE_TIMEOUT),
        ),
        config=ctx.config,
        log=ctx.log,
    )
    ctx.add_router(bind(service))
    # Zil planı değişince haftalık zil önbelleği hemen bırakılır (olay veri yolu, K3).
    ctx.subscribe("bell.week_changed", service.forget_bell_week)
    # Öğrenci grupları değişince tam grup listesinin önbelleği bırakılır.
    ctx.subscribe("bbd_students.groups_changed", service.forget_groups)

    def outbox() -> Any:
        return ctx.try_capability("events.outbox")

    sender = EventSender(store=ctx.store, outbox=outbox,
                         secrets=lambda: ctx.try_capability("secrets"),
                         config=ctx.config, log=ctx.log, settings=service.public_settings)
    ctx.provide("mobile_gateway.events", GatewayEvents(sender))
    box = outbox()
    if box is not None:
        box.on_close(sender.stop)

    async def start() -> None:
        # Diğer modüller yüklensin (kurum ayarının `features` bayrakları onlara bakar).
        await asyncio.sleep(2)
        try:
            await sender.start()
        except Exception as failure:  # noqa: BLE001 — açılış görevi çekirdeği düşürmez (K7)
            ctx.log.error("olay iletimi başlatılamadı", error=str(failure))

    asyncio.get_running_loop().create_task(start(), name="km-gateway-events-start")
    ctx.log.info("mobil geçit hazır", prefix="/api/mobile_gateway/v1")
