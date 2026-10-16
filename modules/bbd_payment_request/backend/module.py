"""Ödeme Talebi — giriş noktası."""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .reports import PaymentReports
from .service import PaymentRequestService


def register(ctx: ModuleContext) -> None:
    # Talep, tahsilat ve şablonlar kantinde durur; geçit olmadan iş yapılamaz.
    canteen = ctx.capability("canteen.api")
    service = PaymentRequestService(canteen=canteen, store=ctx.store, log=ctx.log,
                                    config=ctx.config)
    # PDF raporları (talepler, nakit tahsilat, borçlu listesi — 63-finans-pdf):
    # Masaüstü/Kontrol Merkezi/Raporlar/Kantin/Ödeme talepleri/<yıl>/<ay>.
    reports = PaymentReports(service=service, store=ctx.store, log=ctx.log,
                             fallback_dir=ctx.module_path.parents[1] / "data" / "exports")
    ctx.add_router(bind(service, reports))
    ctx.log.info("ödeme talebi hazır")
