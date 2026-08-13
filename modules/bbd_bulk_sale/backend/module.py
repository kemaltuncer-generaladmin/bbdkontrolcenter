"""Toplu Satış — giriş noktası."""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import BulkSaleService


def register(ctx: ModuleContext) -> None:
    # Satış kantinde yazılır; geçit olmadan bu ekranın yapabileceği bir şey yok.
    canteen = ctx.capability("canteen.api")
    service = BulkSaleService(canteen=canteen, store=ctx.store, log=ctx.log, config=ctx.config)
    ctx.add_router(bind(service))
    ctx.log.info("toplu satış hazır")
