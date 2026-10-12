"""Mobil Geçit — giriş noktası.

`register(ctx)` iş yapmaz: ağa çıkmaz, DB'ye yazmaz. Yetenekler burada
ÇÖZÜLMEZ; `Capabilities` her istekte `ctx.try_capability` ile çözer. Böylece
isteğe bağlı bir sağlayıcı (tablet, zil) düşse ya da sonradan yüklense bile
geçit ayakta kalır ve yalnız ilgili uç 503 `capability_missing` döner (K7).
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .caps import Capabilities
from .service import GatewayService


def register(ctx: ModuleContext) -> None:
    service = GatewayService(
        store=ctx.store,
        caps=Capabilities(ctx.try_capability),
        config=ctx.config,
        log=ctx.log,
    )
    ctx.add_router(bind(service))
    ctx.log.info("mobil geçit hazır", prefix="/api/mobile_gateway/v1")
