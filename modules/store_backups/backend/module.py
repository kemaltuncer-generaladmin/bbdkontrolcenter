"""Manuel Yedekleme — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız
servis kurulur ve router bağlanır. Mağaza erişilemez olsa bile modül yüklenir
ve ekran durumu anlatır (K7).

Bu modül `printer` yeteneği İSTEMEZ: yedek envanteri kâğıda basılmaz, CSV
olarak rapor rafına yazılır.
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import BackupsService

#: Masaüstündeki rapor hiyerarşisinde bu modülün rafı:
#: Raporlar/Mağaza/Denetim/<yıl>/<ay>
CATEGORY = "Mağaza"
SUBCATEGORY = "Denetim"


def register(ctx: ModuleContext) -> None:
    store_api = ctx.capability("store.api")

    service = BackupsService(
        api=store_api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        # Olay veri yolu: "yedek alındı" / "geri yüklendi" haberini duymak
        # isteyen modül olabilir (pano uyarısı). Doğrudan çağrı YOK (K3).
        publish=ctx.publish,
        category=CATEGORY,
        subcategory=SUBCATEGORY,
        fallback_dir=ctx.module_path.parents[1] / "data" / "exports",
    )
    ctx.add_router(bind(service))
    ctx.log.info("manuel yedekleme hazır")
