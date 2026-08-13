"""Ödeme Talebi — giriş noktası."""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import PaymentRequestService


def register(ctx: ModuleContext) -> None:
    # Talep, tahsilat ve şablonlar kantinde durur; geçit olmadan iş yapılamaz.
    canteen = ctx.capability("canteen.api")
    service = PaymentRequestService(canteen=canteen, store=ctx.store, log=ctx.log,
                                    config=ctx.config)
    ctx.add_router(bind(service))
    ctx.log.info("ödeme talebi hazır")
