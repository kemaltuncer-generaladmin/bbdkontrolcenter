"""Promosyonlar — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız servis
kurulur ve router bağlanır. Mağaza erişilemez olsa bile modül yüklenir ve ekran
durumu anlatır (K7).
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import PromotionsService

#: Masaüstündeki rapor hiyerarşisinde bu modülün rafı:
#: Raporlar/Mağaza/Satış/<yıl>/<ay>
CATEGORY = "Mağaza"
SUBCATEGORY = "Satış"


def register(ctx: ModuleContext) -> None:
    store_api = ctx.capability("store.api")
    printer = ctx.try_capability("printer")

    service = PromotionsService(
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
    ctx.add_router(bind(service))
    ctx.log.info("promosyonlar hazır", printer=printer is not None)
