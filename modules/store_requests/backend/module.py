"""Talepler (RMA) — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız
servis kurulur ve router bağlanır. Uzak RMA ucu (`/api/admin/bbd/return-
requests`) henüz yayında olmasa bile modül yüklenir ve ekran durumu anlatır
(K7).
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import RequestsService

#: Masaüstündeki rapor hiyerarşisinde bu modülün rafı:
#: Raporlar/Mağaza/Müşteri/<yıl>/<ay>
CATEGORY = "Mağaza"
SUBCATEGORY = "Müşteri"


def register(ctx: ModuleContext) -> None:
    store_api = ctx.capability("store.api")
    printer = ctx.try_capability("printer")

    service = RequestsService(
        api=store_api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        printer=printer,
        # Onaylanan talep İadeler'e olay yoluyla devredilir (K3): modül modülü
        # import etmez. Dinleyen yoksa devir kaydı yine yerel tabloda kalır.
        publish=ctx.publish,
        category=CATEGORY,
        subcategory=SUBCATEGORY,
        fallback_dir=ctx.module_path.parents[1] / "data" / "exports",
    )
    ctx.add_router(bind(service))
    ctx.log.info("talepler hazır", printer=printer is not None)
