"""Öğle Yemeği — giriş noktası."""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .reports import LunchReports
from .service import LunchService


def register(ctx: ModuleContext) -> None:
    # Kantin geçidi olmadan bu modülün yapabileceği bir şey yok; manifestte
    # zorunlu ilan edildiği için çekirdek onu bizden önce yüklemiş olur.
    canteen = ctx.capability("canteen.api")
    service = LunchService(canteen=canteen, store=ctx.store, log=ctx.log, config=ctx.config)
    # PDF raporları (aylık rapor, gün dökümü — 63-finans-pdf):
    # Masaüstü/Kontrol Merkezi/Raporlar/Kantin/Öğle yemeği/<yıl>/<ay>.
    reports = LunchReports(store=ctx.store, log=ctx.log,
                           fallback_dir=ctx.module_path.parents[1] / "data" / "exports")
    ctx.add_router(bind(service, reports))
    ctx.log.info("öğle yemeği hazır")
