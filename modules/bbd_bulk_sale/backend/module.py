"""Toplu Satış — giriş noktası."""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .reports import BulkSaleReports
from .service import BulkSaleService


def register(ctx: ModuleContext) -> None:
    # Satış kantinde yazılır; geçit olmadan bu ekranın yapabileceği bir şey yok.
    canteen = ctx.capability("canteen.api")
    service = BulkSaleService(canteen=canteen, store=ctx.store, log=ctx.log, config=ctx.config)
    # Parti dökümü PDF'i (63-finans-pdf): Raporlar/Kantin/Toplu satış/<yıl>/<ay>.
    reports = BulkSaleReports(service=service, log=ctx.log,
                              fallback_dir=ctx.module_path.parents[1] / "data" / "exports")
    ctx.add_router(bind(service, reports))
    ctx.log.info("toplu satış hazır")
