"""Raporlar — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız
servis kurulur ve router bağlanır. Mağaza erişilemez olsa bile modül yüklenir
ve ağaç çizilir; yapraklar tek tek "veri alınamadı" der (K7).
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import ReportsService

#: Masaüstündeki rapor hiyerarşisinde bu modülün rafı:
#: Raporlar/Mağaza/Satış/<yıl>/<ay>
#:
#: Modül yedi dalda rapor üretiyor ama TEK rafa yazar. Dal başına klasör
#: açmak, rapor paketini (birden çok dalı tek PDF'te birleştiren çıktı)
#: hangi klasöre koyacağımız sorusunu cevapsız bırakırdı.
CATEGORY = "Mağaza"
SUBCATEGORY = "Satış"

#: Zamanlanmış görevin ulaşabilmesi için canlı servis burada tutulur.
#: `module.yaml → tasks` handler'ı çekirdek tarafından bağlamsız da
#: çağrılabiliyor; o durumda tek tutamak budur.
_LIVE: ReportsService | None = None


def build_service(ctx: ModuleContext) -> ReportsService:
    return ReportsService(
        api=ctx.capability("store.api"),
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        printer=ctx.try_capability("printer"),
        category=CATEGORY,
        subcategory=SUBCATEGORY,
        fallback_dir=ctx.module_path.parents[1] / "data" / "exports",
    )


def live() -> ReportsService | None:
    return _LIVE


def register(ctx: ModuleContext) -> None:
    global _LIVE
    service = build_service(ctx)
    _LIVE = service
    ctx.add_router(bind(service))
    ctx.log.info("raporlar hazır", printer=ctx.try_capability("printer") is not None)
