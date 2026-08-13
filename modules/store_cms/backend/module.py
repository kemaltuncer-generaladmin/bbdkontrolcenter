"""CMS — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız
servis kurulur ve router bağlanır. Mağaza erişilemez olsa bile modül yüklenir
ve ekran durumu anlatır (K7).
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import CmsService

#: Masaüstündeki rapor hiyerarşisinde bu modülün rafı:
#: Raporlar/Mağaza/Müşteri/<yıl>/<ay>
#: "Müşteri" rafı: buradan çıkan iki rapor da (içerik envanteri, yasal
#: metinler) müşteriye gösterilen metinlerin kaydıdır, mali ya da lojistik
#: bir çıktı değildir.
CATEGORY = "Mağaza"
SUBCATEGORY = "Müşteri"


def register(ctx: ModuleContext) -> None:
    store_api = ctx.capability("store.api")
    printer = ctx.try_capability("printer")

    service = CmsService(
        api=store_api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        printer=printer,
        category=CATEGORY,
        subcategory=SUBCATEGORY,
        fallback_dir=ctx.module_path.parents[1] / "data" / "exports",
    )
    ctx.add_router(bind(service))
    ctx.log.info("CMS hazır", printer=printer is not None)
