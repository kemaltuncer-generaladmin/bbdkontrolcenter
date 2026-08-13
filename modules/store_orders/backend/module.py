"""Siparişler — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız servis
kurulur, yetenek deftere yazılır ve router bağlanır. Mağaza erişilemez olsa bile
modül yüklenir ve ekran durumu anlatır (K7).
"""

from __future__ import annotations

from typing import Any

from km_sdk import ModuleContext

from .api.routes import bind
from .service import OrdersService

#: Masaüstündeki rapor hiyerarşisinde bu modülün rafı:
#: Raporlar/Mağaza/Satış/<yıl>/<ay>
CATEGORY = "Mağaza"
SUBCATEGORY = "Satış"


class OrderCard:
    """`store.order.card` yeteneğinin gövdesi.

    Müşteriler ve Talepler ekranları bir siparişin künyesini buradan okur; K3
    gereği o modüller bu modülü import ETMEZ. Yetenek SALT OKURDUR: sipariş
    yazan hiçbir yordam dışarı verilmez, yazma her zaman bu modülün kendi
    uçlarından ve kendi izinlerinden geçer.
    """

    def __init__(self, service: OrdersService) -> None:
        self._service = service

    async def card(self, order_id: int) -> dict[str, Any]:
        return await self._service.card(int(order_id))


def register(ctx: ModuleContext) -> None:
    store_api = ctx.capability("store.api")
    printer = ctx.try_capability("printer")

    service = OrdersService(
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
    ctx.provide("store.order.card", OrderCard(service))
    ctx.add_router(bind(service))
    ctx.log.info("siparişler hazır", printer=printer is not None)
