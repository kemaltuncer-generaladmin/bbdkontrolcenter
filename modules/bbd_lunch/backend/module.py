"""Öğle Yemeği — giriş noktası."""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import LunchService


def register(ctx: ModuleContext) -> None:
    # Kantin geçidi olmadan bu modülün yapabileceği bir şey yok; manifestte
    # zorunlu ilan edildiği için çekirdek onu bizden önce yüklemiş olur.
    canteen = ctx.capability("canteen.api")
    service = LunchService(canteen=canteen, store=ctx.store, log=ctx.log, config=ctx.config)
    ctx.add_router(bind(service))
    ctx.log.info("öğle yemeği hazır")
