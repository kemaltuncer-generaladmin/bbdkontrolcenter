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
    # KM ekranındaki kısıt / ek süre / profil yazmaları outbox'a olay yazar
    # (km-merkezi-senkron §2.2, ADR 0029); yetenek çağrı anında çözülür.
    ctx.add_router(bind(service, outbox=lambda: ctx.try_capability("events.outbox")))
    # Başka modüller (ör. mobil geçit) tabletleri yalnız bu yeteneklerden
    # görür (K3/K5); sözleşme `backend/capabilities.py` başlığında.
    ctx.provide("bbd_tablet.read", TabletRead(service))
    ctx.provide("bbd_tablet.control", TabletControl(service, ctx.log))
    ctx.log.info("öğrenci tablet yönetimi hazır")
