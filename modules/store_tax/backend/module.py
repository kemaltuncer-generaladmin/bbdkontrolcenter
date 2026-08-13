"""Vergilendirme — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız servis
kurulur, yetenek ilan edilir ve router bağlanır. Mağaza erişilemez olsa bile
modül yüklenir ve ekran durumu anlatır (K7).
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .capability import TaxRates
from .service import TaxService

#: Masaüstündeki rapor hiyerarşisinde bu modülün rafı:
#: Raporlar/Mağaza/Finans/<yıl>/<ay>
CATEGORY = "Mağaza"
SUBCATEGORY = "Finans"


def register(ctx: ModuleContext) -> None:
    store_api = ctx.capability("store.api")
    printer = ctx.try_capability("printer")

    service = TaxService(
        api=store_api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        printer=printer,
        category=CATEGORY,
        subcategory=SUBCATEGORY,
        fallback_dir=ctx.module_path.parents[1] / "data" / "exports",
    )
    rates = TaxRates(service, ctx.log, ttl=int(ctx.config.get("capability_ttl") or 60))
    ctx.provide("store.tax.rates", rates)
    ctx.add_router(bind(service, rates))
    ctx.log.info("vergilendirme hazır", printer=printer is not None)
