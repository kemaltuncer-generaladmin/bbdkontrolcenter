"""Ana Ekran Görselleri — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız servis
kurulur ve router bağlanır. Mağaza erişilemez olsa bile modül yüklenir ve ekran
durumu anlatır (K7).
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import HomeMediaService

#: Masaüstündeki rapor hiyerarşisinde bu modülün rafı:
#: Raporlar/Mağaza/Müşteri/<yıl>/<ay>
#:
#: NEDEN "Müşteri": ana sayfa yerleşimi ne satış ne ürün kaydıdır; müşterinin
#: mağazada gördüğü ilk ekranın belgesidir ve vitrin raporlarıyla aynı rafta
#: durur. Raf adı ekranın kendisini değil, çıktının nereye konacağını söyler.
CATEGORY = "Mağaza"
SUBCATEGORY = "Müşteri"


def register(ctx: ModuleContext) -> None:
    store_api = ctx.capability("store.api")
    printer = ctx.try_capability("printer")

    service = HomeMediaService(
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
    ctx.log.info("ana ekran görselleri hazır", printer=printer is not None)
