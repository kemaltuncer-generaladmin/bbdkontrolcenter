"""UDİT İşlem Kayıtları — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız servis
kurulur, yetenek deftere yazılır ve router bağlanır. Mağaza erişilemez olsa
bile modül yüklenir; ekran o zaman geçidin yerel izini gösterir (K7).
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import AuditProvider, AuditService

#: Masaüstündeki rapor hiyerarşisinde bu modülün rafı:
#: Raporlar/Mağaza/Denetim/<yıl>/<ay>
CATEGORY = "Mağaza"
SUBCATEGORY = "Denetim"


def register(ctx: ModuleContext) -> None:
    store_api = ctx.capability("store.api")
    printer = ctx.try_capability("printer")

    service = AuditService(
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
    # 19 panelin çekmecesindeki "İşlem geçmişi" sekmesi bu yeteneğe bağlanır.
    # Panel tarafındaki eşi `ui/panel/index.js` içindeki `capabilities()`
    # dışa vurumudur; ikisi aynı imzayı taşır (K3: modül modülü import etmez).
    ctx.provide("store.audit.for", AuditProvider(service))
    ctx.log.info("denetim kayıtları hazır", printer=printer is not None)
