"""Mobil Geçit — giriş noktası.

`register(ctx)` iş yapmaz: ağa çıkmaz, DB'ye yazmaz. Yetenekler burada
ÇÖZÜLMEZ; `Capabilities` her istekte `ctx.try_capability` ile çözer. Böylece
isteğe bağlı bir sağlayıcı (tablet, zil) düşse ya da sonradan yüklense bile
geçit ayakta kalır ve yalnız ilgili uç 503 `capability_missing` döner (K7).
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .caps import READ_TIMEOUT, WRITE_TIMEOUT, Capabilities
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
    ctx.log.info("mobil geçit hazır", prefix="/api/mobile_gateway/v1")
