"""Deneme Kulübü — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız
servis kurulur ve router bağlanır. Mağaza erişilemez olsa bile modül yüklenir
ve ekran durumu anlatır (K7).
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import TrialClubService

#: Masaüstündeki rapor hiyerarşisinde bu modülün rafı:
#: Raporlar/Mağaza/Müşteri/<yıl>/<ay>
CATEGORY = "Mağaza"
SUBCATEGORY = "Müşteri"


def register(ctx: ModuleContext) -> None:
    store_api = ctx.capability("store.api")
    printer = ctx.try_capability("printer")
    # Tek kişilik hatırlatma bu yetenekten geçer; yoksa o satır eylemi
    # ekranda gizlenir ve modül çalışmaya devam eder.
    notifier = ctx.try_capability("store.notify.send")

    service = TrialClubService(
        api=store_api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        printer=printer,
        notifier=notifier,
        publish=ctx.publish,
        category=CATEGORY,
        subcategory=SUBCATEGORY,
        fallback_dir=ctx.module_path.parents[1] / "data" / "exports",
    )
    ctx.add_router(bind(service))
    ctx.log.info("deneme kulübü hazır", printer=printer is not None,
                 notify=notifier is not None)
