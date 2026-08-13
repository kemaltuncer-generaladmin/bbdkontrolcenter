"""Kantin Ürünleri — giriş noktası."""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import ProductService


def register(ctx: ModuleContext) -> None:
    # Ürünler kantinde durur; geçit olmadan bu ekranın yapabileceği bir şey yok.
    canteen = ctx.capability("canteen.api")
    service = ProductService(canteen=canteen, store=ctx.store, log=ctx.log)
    ctx.add_router(bind(service))
    ctx.log.info("kantin ürünleri hazır")
