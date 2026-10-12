"""Module registration. The canteen remains the student identity authority."""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .capabilities import TabletControl, TabletRead
from .service import TabletService


def register(ctx: ModuleContext) -> None:
    service = TabletService(
        store=ctx.store,
        canteen=ctx.capability("canteen.api"),
        config=ctx.config,
    )
    ctx.add_router(bind(service))
    # Başka modüller (ör. mobil geçit) tabletleri yalnız bu yeteneklerden
    # görür (K3/K5); sözleşme `backend/capabilities.py` başlığında.
    ctx.provide("bbd_tablet.read", TabletRead(service))
    ctx.provide("bbd_tablet.control", TabletControl(service, ctx.log))
    ctx.log.info("öğrenci tablet yönetimi hazır")
