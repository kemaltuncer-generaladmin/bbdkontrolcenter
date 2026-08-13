"""SMS Sistemi — giriş noktası."""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import SmsService


def register(ctx: ModuleContext) -> None:
    # SMS kantinin Netgsm ucundan gider; geçit olmadan gönderilecek bir şey yok.
    canteen = ctx.capability("canteen.api")
    service = SmsService(canteen=canteen, store=ctx.store, log=ctx.log, config=ctx.config)
    ctx.add_router(bind(service))
    ctx.log.info("sms sistemi hazır")
