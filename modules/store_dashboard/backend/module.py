"""Kontrol Paneli — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız
servis kurulur ve router bağlanır. Mağaza erişilemez olsa bile modül yüklenir
ve pano her kartın durumunu tek tek anlatır (K7).
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import DashboardService

#: Masaüstündeki rapor hiyerarşisinde bu modülün rafı:
#: Raporlar/Mağaza/Satış/<yıl>/<ay>
CATEGORY = "Mağaza"
SUBCATEGORY = "Satış"


def register(ctx: ModuleContext) -> None:
    store_api = ctx.capability("store.api")
    printer = ctx.try_capability("printer")
    # Pano özeti dönemin siparişlerini sayfa sayfa geziyor; istekte koşmaz,
    # geçidin arka plan tazeleyicisine verilir. Kendi köşesinde çalışır ki
    # başka bir mağaza ekranının anahtarlarıyla karışmasın.
    scan = ctx.capability("store.scan").scoped("store_dashboard")

    service = DashboardService(
        api=store_api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        printer=printer,
        scan=scan,
        category=CATEGORY,
        subcategory=SUBCATEGORY,
        fallback_dir=ctx.module_path.parents[1] / "data" / "exports",
    )
    ctx.add_router(bind(service))
    ctx.log.info("kontrol paneli hazır", printer=printer is not None)
