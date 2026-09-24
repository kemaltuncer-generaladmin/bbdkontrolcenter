"""Module registration. The canteen remains the student identity authority."""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import TabletService


def register(ctx: ModuleContext) -> None:
    service = TabletService(
        store=ctx.store,
        canteen=ctx.capability("canteen.api"),
        config=ctx.config,
    )
    ctx.add_router(bind(service))
    ctx.log.info("öğrenci tablet yönetimi hazır")
