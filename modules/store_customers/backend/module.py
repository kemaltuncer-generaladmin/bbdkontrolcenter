"""Müşteriler — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız servis
kurulur, yetenek sunulur ve router bağlanır. Mağaza erişilemez olsa bile modül
yüklenir ve ekran durumu anlatır (K7).
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import CustomerCard, CustomersService

#: Masaüstündeki rapor hiyerarşisinde bu modülün rafı:
#: Raporlar/Mağaza/Müşteri/<yıl>/<ay>
CATEGORY = "Mağaza"
SUBCATEGORY = "Müşteri"


def register(ctx: ModuleContext) -> None:
    store_api = ctx.capability("store.api")
    printer = ctx.try_capability("printer")
    # Ağır taramalar (nüfus, sipariş toplulaştırması) isteğin İÇİNDE koşmaz;
    # geçidin arka plan tazeleyicisine verilir. Kendi köşesinde çalışır ki
    # başka bir mağaza ekranının "population" anahtarıyla karışmasın.
    scan = ctx.capability("store.scan").scoped("store_customers")

    service = CustomersService(
        api=store_api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        printer=printer,
        scan=scan,
        publish=ctx.publish,
        category=CATEGORY,
        subcategory=SUBCATEGORY,
        fallback_dir=ctx.module_path.parents[1] / "data" / "exports",
    )
    # Sipariş/iade/talep ekranları müşteri künyesini buradan alır; hiçbiri bu
    # modülü import etmez ve hiçbiri kendi müşteri isteğini atmaz (K3, K4).
    ctx.provide("store.customer.card", CustomerCard(service))
    ctx.add_router(bind(service))
    ctx.log.info("müşteriler hazır", printer=printer is not None)
