"""Fatura — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız
servis kurulur, yetenek ilan edilir ve router bağlanır. Mağaza erişilemez olsa
bile modül yüklenir ve ekran durumu anlatır (K7).
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import InvoiceByOrder, InvoicesService

#: Masaüstündeki rapor hiyerarşisinde bu modülün rafı:
#: Raporlar/Mağaza/Finans/<yıl>/<ay>
CATEGORY = "Mağaza"
SUBCATEGORY = "Finans"


def register(ctx: ModuleContext) -> None:
    store_api = ctx.capability("store.api")
    printer = ctx.try_capability("printer")

    service = InvoicesService(
        api=store_api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        printer=printer,
        publish=ctx.publish,
        category=CATEGORY,
        subcategory=SUBCATEGORY,
        fallback_dir=ctx.module_path.parents[1] / "data" / "exports",
    )
    # Yetenek olarak servisin TAMAMI verilmez: başka modül yazma uçlarına
    # erişmemeli. `InvoiceByOrder` yalnız okur (K3).
    ctx.provide("store.invoice.byOrder", InvoiceByOrder(service))
    ctx.add_router(bind(service))
    ctx.log.info("fatura hazır", printer=printer is not None)
